"""
Club management commands for administrators
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging
from datetime import time

from models import Club
from config.settings import TIMEZONE

logger = logging.getLogger(__name__)


class ClubManagementCommands(commands.Cog):
    """Commands for managing multiple clubs"""
    
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="add_club", description="Register a new club (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_club(self, interaction: discord.Interaction, 
                       club_name: str,
                       circle_id: str = None,
                       daily_quota: int = 1000000,
                       scrape_time: str = "16:00",
                       timezone: str = "Europe/Amsterdam"):
        """
        Add a new club to track
        
        Args:
            club_name: Display name for the club (e.g., "Horsecore")
            circle_id: Uma.moe circle_id (numeric, e.g., "860280110") - OPTIONAL
            daily_quota: Default daily fan quota (default: 1,000,000)
            scrape_time: Time to scrape in HH:MM format (default: 16:00)
            timezone: Timezone for schedule (default: Europe/Amsterdam)
        """
        await interaction.response.defer()
        
        try:
            # Validate circle_id if provided
            if circle_id and not circle_id.isdigit():
                await interaction.followup.send(
                    f"❌ Invalid Circle ID format: `{circle_id}`\n\n"
                    f"The circle_id must be a **numeric ID** from Uma.moe.\n\n"
                    f"**How to find it:**\n"
                    f"1. Go to https://uma.moe/circles/\n"
                    f"2. Search for your club\n"
                    f"3. Click on it and copy the **number** from the URL\n"
                    f"   Example: `https://uma.moe/circles/860280110` → use `860280110`\n\n"
                    f"You can also add the club without a circle_id and add it later with `/edit_club`"
                )
                return
            
            # Check if club already exists
            existing = await Club.get_by_name(club_name)
            if existing:
                await interaction.followup.send(f"❌ Club '{club_name}' already exists")
                return
            
            # Build scrape URL (keep for ChronoGenesis fallback)
            if circle_id:
                scrape_url = f"https://chronogenesis.net/club_profile?circle_id={circle_id}"
            else:
                scrape_url = f"https://chronogenesis.net/club_profile?circle_id={club_name}"
            
            # Validate scrape time format
            try:
                hour, minute = map(int, scrape_time.split(':'))
                if not (0 <= hour < 24 and 0 <= minute < 60):
                    raise ValueError
                scrape_time_obj = time(hour=hour, minute=minute)
            except:
                await interaction.followup.send("❌ Invalid time format. Use HH:MM (e.g., 16:00)")
                return
            
            # Create club
            club = await Club.create(
                club_name=club_name,
                scrape_url=scrape_url,
                circle_id=circle_id,
                daily_quota=daily_quota,
                timezone=timezone,
                scrape_time=scrape_time_obj
            )
            
            # Format quota
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
                      f"**Circle ID:** {circle_id or 'Not set (will use ChronoGenesis)'}\n"
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
            
            if circle_id:
                embed.add_field(
                    name="✅ Uma.moe API Enabled",
                    value=f"Fast scraping via Uma.moe API: https://uma.moe/circles/{circle_id}",
                    inline=False
                )
            else:
                embed.add_field(
                    name="ℹ️ ChronoGenesis Scraper",
                    value="No circle_id set - will use slower web scraping. Add circle_id later with `/edit_club` for better performance.",
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
            logger.info(f"Club '{club_name}' added by {interaction.user} (circle_id: {circle_id})")
            
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
        """List all clubs"""
        await interaction.response.defer()
        
        try:
            clubs = await Club.get_all()
            
            if not clubs:
                await interaction.followup.send("No clubs registered yet. Use `/add_club` to add one.")
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
                
                # Check if circle_id is valid
                scraper_info = ""
                if club.circle_id:
                    if club.is_circle_id_valid():
                        scraper_info = f"\n**Scraper:** Uma.moe API 🚀"
                    else:
                        scraper_info = f"\n**Scraper:** ⚠️ Invalid circle_id"
                else:
                    scraper_info = f"\n**Scraper:** ChronoGenesis"
                
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
                # Validate and convert to time object
                try:
                    hour, minute = map(int, scrape_time.split(':'))
                    if not (0 <= hour < 24 and 0 <= minute < 60):
                        raise ValueError
                    updates['scrape_time'] = time(hour=hour, minute=minute)
                except:
                    await interaction.followup.send("❌ Invalid time format. Use HH:MM (e.g., 16:00)")
                    return
            if timezone is not None:
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
    async def club_autocomplete(self, interaction: discord.Interaction, current: str):
        """Autocomplete for club names"""
        try:
            club_names = await Club.get_all_names()
            return [
                app_commands.Choice(name=name, value=name)
                for name in club_names
                if current.lower() in name.lower()
            ][:25]
        except:
            return []
    
    # Add autocomplete to commands
    remove_club.autocomplete('club')(club_autocomplete)
    activate_club.autocomplete('club')(club_autocomplete)
    edit_club.autocomplete('club')(club_autocomplete)


async def setup(bot):
    """Setup function for loading the cog"""
    await bot.add_cog(ClubManagementCommands(bot))
