"""
Club management commands (add, remove, edit, list)
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, time
import logging
import pytz

from models import Club

logger = logging.getLogger(__name__)


class ClubManagementCommands(commands.Cog):
    """Commands for managing club registrations"""
    
    def __init__(self, bot):
        self.bot = bot
    
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
    
    @app_commands.command(name="add_club", description="Register a new club to track (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_club(self, interaction: discord.Interaction,
                       club_name: str,
                       scrape_url: str,
                       circle_id: str = None,
                       daily_quota: int = 1000000,
                       timezone: str = "Europe/Amsterdam",
                       scrape_time: str = "16:00"):
        """Register a new club"""
        await interaction.response.defer()
        
        try:
            # Check for duplicate
            existing = await Club.get_by_name(club_name)
            if existing:
                await interaction.followup.send(f"❌ Club '{club_name}' already exists")
                return
            
            # Validate circle_id format if provided
            if circle_id is not None and circle_id != "" and not circle_id.isdigit():
                await interaction.followup.send(
                    f"❌ Invalid Circle ID format: `{circle_id}`\n\n"
                    f"The circle_id must be a **numeric ID** from Uma.moe.\n\n"
                    f"**How to find it:**\n"
                    f"1. Go to https://uma.moe/circles/\n"
                    f"2. Search for **{club_name}**\n"
                    f"3. Click on it and copy the **number** from the URL\n"
                    f"   Example: `https://uma.moe/circles/860280110` → use `860280110`"
                )
                return
            
            # Validate timezone
            try:
                pytz.timezone(timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                await interaction.followup.send(f"❌ Invalid timezone: `{timezone}`")
                return
            
            # Parse scrape time
            try:
                hour, minute = map(int, scrape_time.split(':'))
                if not (0 <= hour < 24 and 0 <= minute < 60):
                    raise ValueError
                scrape_time_obj = time(hour=hour, minute=minute)
            except (ValueError, AttributeError):
                await interaction.followup.send("❌ Invalid scrape time format. Use HH:MM (e.g., 16:00)")
                return
            
            # Normalise circle_id: treat empty string as None
            resolved_circle_id = circle_id if circle_id and circle_id != "" else None
            
            club = await Club.create(
                club_name=club_name,
                scrape_url=scrape_url,
                circle_id=resolved_circle_id,
                guild_id=interaction.guild_id,
                daily_quota=daily_quota,
                timezone=timezone,
                scrape_time=scrape_time_obj
            )
            
            # Format quota for display
            if daily_quota >= 1_000_000:
                quota_formatted = f"{daily_quota / 1_000_000:.1f}M"
            elif daily_quota >= 1_000:
                quota_formatted = f"{daily_quota / 1_000:.1f}K"
            else:
                quota_formatted = str(daily_quota)
            
            embed = discord.Embed(
                title="✅ Club Added",
                description=f"Successfully registered **{club_name}**",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(
                name="Club Details",
                value=f"**Name:** {club_name}\n"
                      f"**Circle ID:** {resolved_circle_id or 'Not set'}\n"
                      f"**URL:** {scrape_url}",
                inline=False
            )
            
            embed.add_field(
                name="Settings",
                value=f"**Daily Quota:** {quota_formatted} fans\n"
                      f"**Scrape Time:** {scrape_time} {timezone}\n"
                      f"**Bomb Rules:** 3 days trigger, 7 days countdown",
                inline=False
            )
            
            # Show scraper info based on whether circle_id was provided
            if resolved_circle_id:
                embed.add_field(
                    name="🚀 Scraper",
                    value="Using Uma.moe API (fast path)",
                    inline=False
                )
            else:
                embed.add_field(
                    name="⚠️ Scraper",
                    value="Using ChronoGenesis scraper.\n"
                          "Add circle_id later with `/edit_club` for better performance.",
                    inline=False
                )
            
            embed.add_field(
                name="Next Steps",
                value=f"1. Set channels: `/set_report_channel club:{club_name}` and `/set_alert_channel club:{club_name}`\n"
                      f"2. Adjust settings: `/edit_club club:{club_name}`\n"
                      f"3. Manual check: `/force_check club:{club_name}`",
                inline=False
            )
            
            embed.set_footer(text=f"Added by {interaction.user}")
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Club '{club_name}' added by {interaction.user} (circle_id: {resolved_circle_id}, guild_id: {interaction.guild_id})")
            
        except Exception as e:
            logger.error(f"Error in add_club: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="remove_club", description="Deactivate a club (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_club(self, interaction: discord.Interaction, club: str):
        """Deactivate a club (data is preserved)"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return
            
            if not club_obj.is_active:
                await interaction.followup.send(f"ℹ️ Club '{club}' is already deactivated")
                return
            
            await club_obj.deactivate()
            
            embed = discord.Embed(
                title="✅ Club Deactivated",
                description=f"**{club}** has been deactivated",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(
                name="ℹ️ What this means",
                value="• Daily scraping stopped\n"
                      "• Member data preserved\n"
                      "• Can be reactivated with `/activate_club`",
                inline=False
            )
            
            embed.set_footer(text=f"Deactivated by {interaction.user}")
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Club '{club}' deactivated by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in remove_club: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="activate_club", description="Reactivate a club (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def activate_club(self, interaction: discord.Interaction, club: str):
        """Reactivate a deactivated club"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return
            
            if club_obj.is_active:
                await interaction.followup.send(f"ℹ️ Club '{club}' is already active")
                return
            
            await club_obj.activate()
            
            embed = discord.Embed(
                title="✅ Club Reactivated",
                description=f"**{club}** has been reactivated",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(
                name="ℹ️ What's next",
                value="Daily scraping will resume at the scheduled time.",
                inline=False
            )
            
            embed.set_footer(text=f"Reactivated by {interaction.user}")
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Club '{club}' reactivated by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in activate_club: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="list_clubs", description="View all registered clubs")
    async def list_clubs(self, interaction: discord.Interaction):
        """List clubs registered in this server"""
        await interaction.response.defer()
        
        try:
            # Only show clubs belonging to the current guild (plus any pre-migration clubs)
            clubs = await Club.get_all_for_guild(interaction.guild_id)
            
            if not clubs:
                await interaction.followup.send("No clubs registered in this server. Use `/add_club` to add one.")
                return
            
            embed = discord.Embed(
                title="🏆 Registered Clubs",
                description=f"Total: {len(clubs)} club{'s' if len(clubs) != 1 else ''}",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            for club in clubs:
                status = "✅ Active" if club.is_active else "❌ Inactive"
                
                # Format quota
                if club.daily_quota >= 1_000_000:
                    quota_formatted = f"{club.daily_quota / 1_000_000:.1f}M"
                elif club.daily_quota >= 1_000:
                    quota_formatted = f"{club.daily_quota / 1_000:.1f}K"
                else:
                    quota_formatted = str(club.daily_quota)
                
                # Scraper type indicator
                if club.circle_id:
                    if club.is_circle_id_valid():
                        scraper_info = "\n**Scraper:** Uma.moe API 🚀"
                    else:
                        scraper_info = "\n**Scraper:** ⚠️ Invalid circle_id"
                else:
                    scraper_info = "\n**Scraper:** ChronoGenesis"
                
                embed.add_field(
                    name=f"{status} {club.club_name}",
                    value=f"**Quota:** {quota_formatted} fans/day\n"
                          f"**Schedule:** {club.get_scrape_time_str()} {club.timezone}"
                          f"{scraper_info}",
                    inline=False
                )
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in list_clubs: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="edit_club", description="Edit club settings (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def edit_club(self, interaction: discord.Interaction, 
                       club: str,
                       circle_id: str = None,
                       daily_quota: int = None,
                       scrape_time: str = None,
                       timezone: str = None,
                       bomb_trigger_days: int = None,
                       bomb_countdown_days: int = None):
        """Edit club configuration"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return
            
            # Validate circle_id if being updated
            if circle_id is not None and circle_id != "" and not circle_id.isdigit():
                await interaction.followup.send(
                    f"❌ Invalid Circle ID format: `{circle_id}`\n\n"
                    f"The circle_id must be a **numeric ID** from Uma.moe.\n\n"
                    f"**How to find it:**\n"
                    f"1. Go to https://uma.moe/circles/\n"
                    f"2. Search for **{club}**\n"
                    f"3. Click on it and copy the **number** from the URL\n"
                    f"   Example: `https://uma.moe/circles/860280110` → use `860280110`\n\n"
                    f"To remove circle_id (use ChronoGenesis), use an empty string."
                )
                return
            
            updates = {}
            if circle_id is not None:
                updates['circle_id'] = circle_id if circle_id != "" else None
            if daily_quota is not None:
                updates['daily_quota'] = daily_quota
            if scrape_time is not None:
                try:
                    hour, minute = map(int, scrape_time.split(':'))
                    if not (0 <= hour < 24 and 0 <= minute < 60):
                        raise ValueError
                    updates['scrape_time'] = time(hour=hour, minute=minute)
                except (ValueError, AttributeError):
                    await interaction.followup.send("❌ Invalid time format. Use HH:MM (e.g., 16:00)")
                    return
            if timezone is not None:
                try:
                    pytz.timezone(timezone)
                except pytz.exceptions.UnknownTimeZoneError:
                    await interaction.followup.send(f"❌ Invalid timezone: `{timezone}`")
                    return
                updates['timezone'] = timezone
            if bomb_trigger_days is not None:
                updates['bomb_trigger_days'] = bomb_trigger_days
            if bomb_countdown_days is not None:
                updates['bomb_countdown_days'] = bomb_countdown_days
            
            if not updates:
                await interaction.followup.send("❌ No changes specified")
                return
            
            await club_obj.update_settings(**updates)
            
            embed = discord.Embed(
                title="✅ Club Settings Updated",
                description=f"Successfully updated **{club}**",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            changes_text = []
            for key, value in updates.items():
                if key == 'circle_id':
                    if value:
                        changes_text.append(f"**Circle ID:** {value} (Uma.moe API enabled 🚀)")
                    else:
                        changes_text.append(f"**Circle ID:** Removed (will use ChronoGenesis)")
                elif key == 'daily_quota':
                    if value >= 1_000_000:
                        formatted = f"{value / 1_000_000:.1f}M"
                    elif value >= 1_000:
                        formatted = f"{value / 1_000:.1f}K"
                    else:
                        formatted = str(value)
                    changes_text.append(f"**Daily Quota:** {formatted} fans")
                elif key == 'scrape_time':
                    changes_text.append(f"**Scrape Time:** {value}")
                elif key == 'timezone':
                    changes_text.append(f"**Timezone:** {value}")
                elif key == 'bomb_trigger_days':
                    changes_text.append(f"**Bomb Trigger:** {value} days")
                elif key == 'bomb_countdown_days':
                    changes_text.append(f"**Bomb Countdown:** {value} days")
            
            embed.add_field(
                name="Changes Applied",
                value="\n".join(changes_text),
                inline=False
            )
            
            embed.set_footer(text=f"Updated by {interaction.user}")
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Club '{club}' settings updated by {interaction.user}: {updates}")
            
        except Exception as e:
            logger.error(f"Error in edit_club: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    # Autocomplete for club parameter
    remove_club.autocomplete('club')(club_autocomplete)
    activate_club.autocomplete('club')(club_autocomplete)
    edit_club.autocomplete('club')(club_autocomplete)


async def setup(bot):
    """Setup function for loading the cog"""
    await bot.add_cog(ClubManagementCommands(bot))